  function shape = wincalc(choice,npts,userdef)
% function shape = wincalc(choice,npts,userdef) 
% input window choice number (see avgdef_h.m)
% and frame size in npt
% returns time domain window shape
%
% Dick Benson, DSPT 

    avgdef_h;   % script with window coefficients (in array winconv)
    if choice < MODALSTARTc
       if choice==1
          shape = ones(1,npts);
       else
          win   =  winconv(choice,:);
          lwx   =  length(win);
          shape =  npts*real(ifft([win,zeros(1,npts-(2*lwx-1)),win(lwx:-1:2)]));
       end;
   
    elseif choice == BOX_EXPP1c
       % box car on force, exponential with 0.1 response
       shape      = ones(2,npts);
       shape(2,:) = (0.1^(1/(npts-1))).^(0:(npts-1));
       
    elseif choice == BOX_EXPP01c
       % box car on force, exponential with 0.01 response
       shape       = ones(2,npts);
       shape(2,:)  = (0.01^(1/(npts-1))).^(0:(npts-1));  % 11/16/00 RAB
       
    elseif choice == F20_EXPP1c
        % force 20 percent , exponential with 0.1 response
        l20 = round(0.2*npts);
        shape      = [ones(2,l20),zeros(2,npts-l20)];
        shape(2,:) = (0.1^(1/(npts-1))).^(0:(npts-1));
        
    elseif choice == F20_EXPP01c
        % force 20 percent , exponential with 0.01 response
        l20 = round(0.2*npts);
        shape      = [ones(2,l20),zeros(2,npts-l20)];
        shape(2,:) = (0.01^(1/(npts-1))).^(0:(npts-1));
   
    elseif choice == USERDEFWINc
        % userdefined force and exponential windows 10/12/98
        % force of userdef.forcewin/100 percent , exponential with userdef.expdecay/100  response
        lforce     = round(npts*userdef.forcewin/100);
        shape      = [ones(2,lforce),zeros(2,npts-lforce)];
        shape(2,:) = ((userdef.expdecay/100)^(1/(npts-1))).^(0:(npts-1));
    
    else
        disp(['Choice not recognized in winclac.m   ', choice])

    end;
    
% end function

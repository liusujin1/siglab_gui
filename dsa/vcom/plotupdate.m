function  plotupdate(In1)
% function plotsetup
% This function initializes and updates the plot
% settings based on current vxx setup parameters.

global hMCVIEW;
global ChanDat;
Xscale = 'lin';
Yscale = 'lin'; 
      
if strcmp(In1,'XYupdate')
   set(hMCVIEW.fig,'visible','on');
end;

switch hMCVIEW.disptype

case 'Time'
   % update xdata, ydata, limits, and strings
   if strcmp(In1,'XYupdate')
       xvec = ChanDat.tdxvec;      
       j = 1;
       for i = 1:hMCVIEW.arrange(1)*hMCVIEW.arrange(2)
           match=find(i==ChanDat.clist);
           if ~isempty(match)
               ch = ChanDat.clist(j);
               ylimit = ChanDat.scmeas(ch).fs_val*ChanDat.scmeas(ch).eu_val;
               set(hMCVIEW.axis(ch),'XLim',[xvec(1) xvec(length(xvec))],...
                       'YLim',[-1*ylimit ylimit],...
                       'Xscale',Xscale,...
                       'Yscale',Yscale);
               status = get(hMCVIEW.custom,'checked');
               if strcmp(status,'on')
                  ydata = custom_process(ch);    
               else
                  ydata = ChanDat.scmeas(ch).tdmeas*ChanDat.scmeas(ch).eu_val; 
               end;
               set(hMCVIEW.line(ch),'Xdata',xvec,...
                       'Ydata',ydata);
               set(hMCVIEW.offtext(ch),'color','white','string',num2str(i),...
                       'position',[[xvec(length(xvec))-xvec(1)]/2 ylimit/1.2]);
               set(hMCVIEW.line(ch),'color','green');
               j = j+1;
           else
               if hMCVIEW.arrange(1) == 1
                   rotation = 270;
               else
                   rotation = 0;
               end;
               xlimits = get(hMCVIEW.axis(i),'xlim');
               ylimits = get(hMCVIEW.axis(i),'ylim');
               set(hMCVIEW.line(i),'color','red');
               set(hMCVIEW.offtext(i),'color','red',...
                       'rotation',rotation,...
                       'position',[[xlimits(2)-xlimits(1)]/2 ylimits(2)/1.2],...
                       'string',[num2str(i),' is Off']);    
           end;
       end;
   % only update ydata
   elseif strcmp(In1,'Yupdate')
       if hMCVIEW.arrange(1)*hMCVIEW.arrange(2)>length(ChanDat.clist)
           upperlimit = length(ChanDat.clist);
       else
           upperlimit = hMCVIEW.arrange(1)*hMCVIEW.arrange(2);
       end;
       for i = 1:upperlimit
           ch = ChanDat.clist(i);
           status = get(hMCVIEW.custom,'checked');
           if strcmp(status,'on')
               ydata = custom_process(ch);    
           else
               ydata = ChanDat.scmeas(ch).tdmeas*ChanDat.scmeas(ch).eu_val; 
           end;
           set(hMCVIEW.line(ch),'ydata',ydata);
       end;
   else  
       errordlg('Undefined In1 parameter, plots likely not scaled correctly','Oops!');
   end; 
% spectrum code is not up-to-date with time code, 1/14/98          
case 'Spectrum'
   if strcmp(In1,'XYupdate')
       xvec=ChanDat.fdxvec;    
       for i = 1:length(ChanDat.clist)
           ch = ChanDat.clist(i);
           scale = ChanDat.scmeas(ch).eu_val;
           set(hMCVIEW.axis(ch),  'XLim',[xvec(1) xvec(length(xvec))],...
                       'YLim',[20*log10(20e-3*scale/2^16) 20*log10(10*scale)],...
                       'Xscale',Xscale,...
                       'Yscale',Yscale);
           set(hMCVIEW.line(ch),'Xdata',xvec,...
                       'Ydata',ChanDat.scmeas(ch).fdmeas*ChanDat.scmeas(ch).eu_val);
       end;
   elseif strcmp(In1,'Yupdate')
       for i = 1:length(ChanDat.clist)
           ch = ChanDat.clist(i);
           set(hMCVIEW.line(ch),...
           'ydata',ChanDat.scmeas(ch).fdmeas*ChanDat.scmeas(ch).eu_val); 
       end;
   else
       errordlg('Undefined In1 parameter, plots likely not scaled correctly','Oops!');    
   end;     
otherwise
   errordlg(['Undefined display in plotsetup.m = ',Display],'Oops!');
end;
   
   
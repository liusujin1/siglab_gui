  function f_prompt(Action,vxx,def_exten)
% function f_prompt(Action,vxx,def_exten)
% This function creates file dialog to choose a setup file b4 starting a vi.  
% If a vi is invoked 'vxx ?' AND a valid preferred user path has been
% defined by the file siglab\vcom\vi_path.mat, the file dialog will 
% open with the contents of the directory at the preferred user path. 
% If a vi is invoked 'vxx ??' the file dialog will open in the siglab\vxx directory.
% The preferred path is set with function vip. 
% Dick Benson DSP Technology
    if strcmp(Action,'?')
       % check for the user's preferred path
       vi_path=vip('get','nfg');
       if strcmp(vi_path,'nfg')
          f_str = def_exten;
       else
          f_str = [vi_path,def_exten];
       end;
    elseif strcmp(Action,'??')
       [default_drv,ppath] = pathfind(vxx);
       f_str=[[default_drv,ppath,'\'], def_exten];
    end;
    [file_n,path_n]=uigetfile(f_str,'Open File',0.5,0.5);
    if file_n ~=0
        eval([vxx,'(''init'',''',path_n,''',''',file_n,''')']);
    else
        disp('no file selected')
    end;
% end function

